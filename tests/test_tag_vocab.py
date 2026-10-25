from mockup_generator.prompts.tag_vocab import allowed, groups_for


def test_groups_for_lehenga_excludes_office():
    gs = {g.group: g for g in groups_for("lehenga")}
    assert "office" not in [v for v, _ in gs["occasion"].values]
    assert gs["occasion"].label == "Occasion" and gs["occasion"].single is False
    assert "neck" not in gs  # lehenga has no neck tags in the vocab
    assert {g.group: g for g in groups_for("women_ready")}["neck"].single is True


def test_groups_for_none_is_empty():
    assert groups_for(None) == []


def test_groups_for_values_match_allowed():
    for g in groups_for("saree"):
        assert [v for v, _ in g.values] == allowed(g.group, "saree")
