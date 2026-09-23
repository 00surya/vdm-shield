"""Keep the lead-up and aftermath of an alert in one bounded clip."""
from dataclasses import dataclass


@dataclass
class PendingEvidence:
    record: dict
    annotated: dict[float, bytes]
    raw: dict[float, bytes]
    first_trigger: float
    last_trigger: float
    changed: bool = False


class EvidenceRecorder:
    def __init__(self, post_seconds=4, max_event_seconds=30, max_bytes=48 * 1024 * 1024, max_pending=4):
        self.post_seconds = post_seconds
        self.max_event_seconds = max_event_seconds
        self.max_bytes = max_bytes
        self.max_pending = max_pending
        self.pending = {}

    @staticmethod
    def _frames(frames):
        return dict(frames)

    def begin(self, record, annotated, raw):
        """Call only after the initial incident has entered the storage queue."""
        at = record["signals"]["source_seconds"]
        self.pending[record["id"]] = PendingEvidence(
            record, self._frames(annotated), self._frames(raw), at, at)

    def revise(self, record):
        item = self.pending.get(record["id"])
        if item is None:
            return False
        old = item.record
        history = old["signals"].get("grouped_signals", [{
            "at": item.first_trigger, "score": old["score"], "reasons": old["reasons"]}])
        history = [*history, {"at": record["signals"]["source_seconds"],
                              "score": record["score"], "reasons": record["reasons"]}][-20:]
        item.record = {**record, "reasons": list(dict.fromkeys([*old["reasons"], *record["reasons"]])),
                       "signals": {**old["signals"], **record["signals"], "grouped_signals": history}}
        item.last_trigger = record["signals"]["source_seconds"]
        item.changed = True
        return True

    def append(self, timestamp, annotated=None, raw=None):
        for item in self.pending.values():
            if timestamp <= item.first_trigger:
                continue
            if timestamp > item.first_trigger + self.max_event_seconds:
                continue
            if annotated is not None and timestamp not in item.annotated:
                item.annotated[timestamp] = annotated
                item.changed = True
            if raw is not None and timestamp not in item.raw:
                item.raw[timestamp] = raw
                item.changed = True
            # Long events remain bounded. The oldest lead-up frames go first.
            while sum(map(len, item.annotated.values())) + sum(map(len, item.raw.values())) > self.max_bytes:
                oldest = min((*item.annotated.keys(), *item.raw.keys()))
                item.annotated.pop(oldest, None)
                item.raw.pop(oldest, None)

    def ready_ids(self, timestamp, final=False):
        ids = []
        ordered = sorted(self.pending.items(), key=lambda entry: entry[1].first_trigger)
        for incident_id, item in ordered:
            due = timestamp >= min(item.last_trigger + self.post_seconds,
                                   item.first_trigger + self.max_event_seconds)
            evict = len(self.pending) - len(ids) > self.max_pending
            if not (final or due or evict):
                continue
            ids.append(incident_id)
        return ids

    def payload(self, incident_id):
        item = self.pending[incident_id]
        if not item.changed:
            return None
        return item.record, sorted(item.annotated.items()), sorted(item.raw.items())

    def finish(self, incident_id):
        del self.pending[incident_id]
