"""Run a complete local candidate experiment: train, held-out evaluation, ONNX export/evaluation."""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def completed_epochs(path):
    with Path(path).open(newline='') as stream:
        return [int(row['epoch']) for row in csv.DictReader(stream, skipinitialspace=True)]


def continuation_info(previous, report):
    """Verify completed training and unchanged data before selecting its final weights."""
    from scripts.train_weapons import artifact_digest
    previous = Path(previous).resolve()
    state = json.loads((previous / 'status.json').read_text())
    recipe = json.loads((previous / 'training/run.json').read_text())
    epochs = completed_epochs(previous / 'training/fit/results.csv')
    if not state.get('training_complete') or not epochs or epochs != list(range(1, state['epochs_requested']+1)):
        raise ValueError('Continue only a fully completed training stage with a complete epoch history.')
    if not report['valid'] or report['dataset_sha256'] != recipe['dataset_sha256']:
        raise ValueError('Continuation requires the same audited training, validation and test dataset.')
    weights = previous / 'training/fit/weights/last.pt'
    if not weights.is_file():
        raise ValueError('The final checkpoint is missing.')
    return dict(previous_run=str(previous), input_checkpoint=str(weights),
                input_checkpoint_sha256=artifact_digest(weights),
                prior_epochs=state.get('epochs_before_run', 0)+len(epochs),
                dataset_sha256=report['dataset_sha256'], optimizer_state_restored=False,
                mode='Additional fine-tuning from final weights with a fresh low-learning-rate optimizer')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; never overwrite an experiment')
    parser.add_argument('--weights', type=Path, default=ROOT / 'models/yolo26n.pt')
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--imgsz', type=int, default=512)
    parser.add_argument('--batch', type=int, default=8)
    parser.add_argument('--device', default='mps')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--prepare-public', action='store_true', help='Download and prepare the pinned weapon+ordnance dataset first')
    parser.add_argument('--continue-from', type=Path, help='Completed experiment directory; --epochs is the number of ADDITIONAL epochs')
    args = parser.parse_args()
    if args.epochs < 1 or args.batch < 1 or args.threads < 1 or args.imgsz < 320 or args.imgsz % 32:
        parser.error('Use positive epochs/batch/threads and imgsz >=320 divisible by 32.')
    if args.continue_from and args.prepare_public:
        parser.error('Continuation uses the existing dataset; omit --prepare-public.')
    args.data, args.output, args.weights = [p.resolve() for p in (args.data, args.output, args.weights)]
    args.output.mkdir(parents=True, exist_ok=False)
    state = dict(status='starting', started_at=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
                 epochs_requested=args.epochs, epochs_before_run=0, epochs_completed=0,
                 total_epochs_completed=0, total_epochs_target=args.epochs, candidate_only=True,
                 settings={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})

    def status(**updates):
        state.update(updates, updated_at=datetime.now(timezone.utc).isoformat())
        temporary = args.output / 'status.json.tmp'
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(args.output / 'status.json')

    def run_stage(name, command):
        log = args.output / f'{name}.log'
        status(status='running', stage=name, log=str(log))
        print(f'{name}: {log}', flush=True)
        with log.open('w') as stream:
            child = subprocess.Popen([sys.executable, '-u', *map(str, command)], cwd=ROOT,
                                     stdout=stream, stderr=subprocess.STDOUT)
            status(child_pid=child.pid)
            while child.poll() is None:
                time.sleep(10)
                results = args.output / 'training/fit/results.csv'
                if name == 'train' and results.exists():
                    try:
                        done = len(completed_epochs(results))
                        status(epochs_completed=done, total_epochs_completed=state['epochs_before_run']+done)
                    except (OSError, ValueError):
                        pass  # A metrics row may still be in the process of being written.
            returncode = child.returncode
        if returncode:
            raise RuntimeError(f'{name} exited with {returncode}; inspect {log}')

    common = ['--data', args.data, '--imgsz', args.imgsz, '--threads', args.threads, '--allow-upstream-annotations']
    try:
        fine_tune = []
        if args.continue_from:
            from scripts.train_weapons import audit
            status(status='running', stage='verify-continuation')
            lineage = continuation_info(args.continue_from, audit(args.data, args.imgsz, allow_upstream=True))
            args.weights = Path(lineage['input_checkpoint'])
            (args.output / 'lineage.json').write_text(json.dumps(lineage, indent=2) + '\n')
            state['settings']['weights'] = str(args.weights)
            status(epochs_before_run=lineage['prior_epochs'], total_epochs_completed=lineage['prior_epochs'],
                   total_epochs_target=lineage['prior_epochs']+args.epochs, continuation=lineage)
            fine_tune = ['--fine-tune']
        if args.prepare_public:
            if args.data.name != 'dataset.yaml' or args.data.parent.exists():
                raise ValueError('--prepare-public requires dataset.yaml inside a new dataset directory')
            run_stage('prepare', ['scripts/prepare_weapon_ordnance_data.py', '--output', args.data.parent])
        run_stage('train', ['scripts/train_weapons.py', 'train', *common,
                  '--weights', args.weights, '--output', args.output / 'training', '--device', args.device,
                  '--epochs', args.epochs, '--batch', args.batch, '--workers', 0, '--patience', 0, '--save-period', 5, *fine_tune])
        epochs = completed_epochs(args.output / 'training/fit/results.csv')
        if epochs != list(range(1, args.epochs+1)):
            raise RuntimeError(f'Expected {args.epochs} completed epochs; observed {epochs}')
        best = args.output / 'training/fit/weights/best.pt'
        status(training_complete=True, epochs_completed=len(epochs),
               total_epochs_completed=state['epochs_before_run']+len(epochs), best_checkpoint=str(best))
        run_stage('test-pytorch', ['scripts/train_weapons.py', 'evaluate', *common, '--weights', best,
                  '--output', args.output / 'test-pytorch', '--device', args.device, '--split', 'test'])
        run_stage('export-onnx', ['scripts/train_weapons.py', 'export', *common, '--weights', best,
                  '--output', args.output / 'onnx', '--device', 'cpu'])
        run_stage('test-onnx', ['scripts/train_weapons.py', 'evaluate', *common,
                  '--weights', args.output / 'onnx/candidate.onnx', '--output', args.output / 'test-onnx',
                  '--device', 'cpu', '--split', 'test'])
        status(status='completed', stage='completed', completed_at=datetime.now(timezone.utc).isoformat(),
               onnx=str(args.output / 'onnx/candidate.onnx'),
               metrics=str(args.output / 'test-pytorch/metrics.json'),
               onnx_metrics=str(args.output / 'test-onnx/metrics.json'))
        print(f'Completed all {len(epochs)} epochs, evaluation and ONNX export: {args.output}', flush=True)
        return 0
    except BaseException as error:
        status(status='failed', error=str(error))
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
