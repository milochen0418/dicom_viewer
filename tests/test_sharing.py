"""Series links, who is on which series, and the shared sliders' keys."""

from __future__ import annotations

import os

import pytest

from dicom_viewer import links, viewers
from dicom_viewer.states.room import slider_keys_after


@pytest.fixture(autouse=True)
def _fresh():
    links._known.clear()
    viewers._on.clear()
    yield
    links._known.clear()
    viewers._on.clear()


def _tree(tmp_path):
    root = tmp_path / "dicom"
    (root / "study" / "ct").mkdir(parents=True)
    (root / "study" / "seg").mkdir()
    (root / ".item" / "hidden").mkdir(parents=True)
    return str(root)


def test_ids_are_short_stable_and_differ(tmp_path):
    root = _tree(tmp_path)
    ct = os.path.join(root, "study", "ct")
    first = links.id_of(ct, root)
    assert links.valid_id(first)
    assert links.id_of(ct + os.sep, root) == first
    assert links.id_of(os.path.join(root, "study", "seg"), root) != first
    assert links.id_of(root, root) != first
    # The same folders under another root (another host) have the same ids.
    other = tmp_path / "elsewhere" / "dicom" / "study" / "ct"
    other.mkdir(parents=True)
    assert links.id_of(str(other), str(tmp_path / "elsewhere" / "dicom")) == first


def test_find_folder_walks_when_the_id_is_new(tmp_path):
    root = _tree(tmp_path)
    ct = os.path.join(root, "study", "ct")
    wanted = links.id_of(ct, root)
    links._known.clear()  # e.g. after a restart
    assert links.find_folder(wanted, root) == ct
    assert links.find_folder(links.id_of(root, root), root) == root
    hidden = links.id_of(os.path.join(root, ".item", "hidden"), root)
    links._known.clear()
    assert links.find_folder(hidden, root) is None
    assert links.find_folder("0123456789", root) is None
    assert links.find_folder("../../etc", root) is None


def test_find_folder_follows_links_without_looping(tmp_path):
    root = _tree(tmp_path)
    data = tmp_path / "data" / "mr"
    data.mkdir(parents=True)
    os.symlink(data.parent, os.path.join(root, "linked"))
    os.symlink(root, os.path.join(root, "study", "loop"))
    wanted = links.id_of(os.path.join(root, "linked", "mr"), root)
    links._known.clear()
    assert links.find_folder(wanted, root) == os.path.join(root, "linked", "mr")


def test_a_moved_folder_is_looked_for_again(tmp_path):
    root = _tree(tmp_path)
    ct = os.path.join(root, "study", "ct")
    wanted = links.id_of(ct, root)
    os.rename(ct, os.path.join(root, "study", "ct2"))
    assert links.find_folder(wanted, root) is None


def test_people_join_leave_and_keep_one_colour_each():
    a1 = viewers.join("s1", "pa1", "ta1", "amy@x", "Amy", now=100)
    a2 = viewers.join("s1", "pa2", "ta2", "amy@x", "Amy", now=101)
    b = viewers.join("s1", "pb", "tb", "guest-1234", "Guest 1234", now=102)
    assert a1.color == a2.color != b.color
    assert [t["id"] for t in viewers.tabs("s1")] == ["pa1", "pa2", "pb"]
    assert viewers.watching() == {"s1": [
        {"user": "amy@x", "name": "Amy", "color": a1.color},
        {"user": "guest-1234", "name": "Guest 1234", "color": b.color},
    ]}
    assert viewers.users_on("s1") == ["amy@x", "guest-1234"]
    assert viewers.tokens_on("s1") == ["tb", "ta2", "ta1"]
    assert viewers.leave("pa1") == "s1"
    assert viewers.leave("pa1") == ""
    assert [t["id"] for t in viewers.tabs("s1")] == ["pa2", "pb"]


def test_a_tab_is_on_one_series_at_a_time():
    viewers.join("s1", "p", "t", "amy@x", "Amy")
    assert viewers.on("p") == "s1"
    viewers.join("s2", "p", "t", "amy@x", "Amy")
    assert viewers.on("p") == "s2"
    assert viewers.tabs("s1") == []
    assert [t["id"] for t in viewers.tabs("s2")] == ["p"]
    assert viewers.watching().keys() == {"s2"}
    viewers.leave("p")
    assert viewers.on("p") == ""


def test_a_tab_found_again_keeps_its_colour():
    viewers.join("s1", "pa", "ta", "amy@x", "Amy")
    b = viewers.join("s1", "pb", "tb", "ben@x", "Ben")
    viewers.leave("pa")
    viewers.join("s1", "pc", "tc", "cat@x", "Cat")  # takes Amy's colour
    again = viewers.join("s1", "pb", "tb2", "ben@x", "Ben")  # Ben reconnects
    assert again.color == b.color
    assert viewers.tokens_on("s1")[0] == "tb2"


def test_idle_tabs_are_dropped_and_come_back_with_their_colour_free():
    viewers.join("s1", "pa", "ta", "amy@x", "Amy", now=100)
    b = viewers.join("s1", "pb", "tb", "ben@x", "Ben", now=100)
    assert viewers.touch("s1", "pa", now=125)
    assert viewers.prune("s1", idle=30, now=135) == ["pb"]
    assert not viewers.touch("s1", "pb", now=136)
    c = viewers.join("s1", "pc", "tc", "cat@x", "Cat", now=137)
    assert c.color == b.color  # free again


def test_only_the_tab_itself_can_say_goodbye():
    viewers.join("s1", "pa", "secret-token", "amy@x", "Amy")
    assert viewers.find("other-token", "pa") == ""
    assert viewers.find("secret-token", "pa") == "s1"


def test_a_drag_keeps_the_draggers_key_and_moves_everyone_elses():
    # Amy and Ben look at slice keys: everyone's is `key`, except the dragger's (`hold`).
    def seen(state, peer):
        key, hold, by = state
        return hold if by == peer else key

    state = (0, 0, "")
    before = seen(state, "amy"), seen(state, "ben")
    state = slider_keys_after(*state, "amy", drag=True)  # Amy starts dragging
    assert seen(state, "amy") == before[0]  # her slider is not remounted mid-drag
    assert seen(state, "ben") != before[1]  # Ben's follows
    ben_key = seen(state, "ben")
    state = slider_keys_after(*state, "amy", drag=True)
    assert seen(state, "amy") == before[0]
    assert seen(state, "ben") != ben_key
    state = slider_keys_after(*state, "ben", drag=False)  # Ben presses a key
    amy_key, ben_key = seen(state, "amy"), seen(state, "ben")
    assert amy_key != before[0]  # Amy's slider moves to Ben's slice
    state = slider_keys_after(*state, "ben", drag=True)  # then Ben drags
    assert seen(state, "ben") == ben_key
    assert seen(state, "amy") != amy_key
