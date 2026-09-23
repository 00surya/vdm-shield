from vmd.episodes import EpisodeGrouper
from vmd.heuristics import Person


def people_at(x, ids):
    return [Person(track_id, (x + offset, 80, x + offset + 50, 220), [(0, 0, 0)] * 17)
            for offset, track_id in zip((0, 35), ids)]


def test_fight_groups_new_track_ids_at_same_location():
    episodes = EpisodeGrouper()
    assert episodes.group("fight", "first", 23, (1, 2), people_at(220, (1, 2)), (480, 640, 3)) == ("first", 1, False)
    assert episodes.group("fight", "second", 27.1, (3, 4), people_at(240, (3, 4)), (480, 640, 3)) == ("first", 2, True)
    assert episodes.group("fight", "third", 28.2, (5, 6), people_at(250, (5, 6)), (480, 640, 3)) == ("first", 3, True)


def test_fight_keeps_distant_and_later_incidents_separate():
    episodes = EpisodeGrouper()
    episodes.group("fight", "first", 2, (1, 2), people_at(20, (1, 2)), (480, 640, 3))
    assert episodes.group("fight", "distant", 3, (3, 4), people_at(430, (3, 4)), (480, 640, 3)) == ("distant", 1, False)
    assert episodes.group("fight", "later", 10, (5, 6), people_at(20, (5, 6)), (480, 640, 3)) == ("later", 1, False)
    assert episodes.group("hands_up", "hands", 11, (5, 5), people_at(20, (5, 6)), (480, 640, 3)) == ("hands", 1, False)
