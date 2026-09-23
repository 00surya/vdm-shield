"""Group repeated fight triggers from one camera into a single incident."""
from dataclasses import dataclass, field
from math import hypot


@dataclass
class FightEpisode:
    incident_id: str
    last_time: float
    center: tuple[float, float] | None
    tracks: set[int] = field(default_factory=set)
    count: int = 1


class EpisodeGrouper:
    def __init__(self, gap_seconds=6.0, location_distance=.32):
        self.gap_seconds = gap_seconds
        self.location_distance = location_distance
        self.fights = []

    def group(self, event_type, incident_id, timestamp, pair, people, frame_shape):
        """Return (stored ID, count, is_update); only fight signals are grouped."""
        if event_type != "fight":
            return incident_id, 1, False
        tracks = set(pair or ())
        matching = [p.center for p in people if p.track_id in tracks]
        height, width = frame_shape[:2]
        center = (sum(x for x, _ in matching) / len(matching) / width,
                  sum(y for _, y in matching) / len(matching) / height) if matching else None
        self.fights = [episode for episode in self.fights
                       if 0 <= timestamp - episode.last_time <= self.gap_seconds]
        candidates = []
        for episode in self.fights:
            shared_track = bool(tracks & episode.tracks)
            near = (center is not None and episode.center is not None and
                    hypot(center[0] - episode.center[0], center[1] - episode.center[1]) <= self.location_distance)
            if shared_track or near:
                distance = hypot(center[0] - episode.center[0], center[1] - episode.center[1]) if center and episode.center else 1.0
                candidates.append((shared_track, -distance, episode.last_time, episode))
        if candidates:
            episode = max(candidates, key=lambda item: item[:3])[3]
            episode.last_time = timestamp
            episode.center = center or episode.center
            episode.tracks.update(tracks)
            episode.count += 1
            return episode.incident_id, episode.count, True
        self.fights.append(FightEpisode(incident_id, timestamp, center, tracks))
        return incident_id, 1, False

    def release(self, incident_id):
        self.fights = [episode for episode in self.fights if episode.incident_id != incident_id]
