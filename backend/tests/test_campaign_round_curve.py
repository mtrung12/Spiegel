"""The round curve must cover every round in the run, silent ones included."""

from app.services.campaign_metrics import CampaignKPIs, CampaignMetricsService


def test_silent_rounds_get_a_zero_bar():
    # Rounds 2 and 4 logged nothing, so the timeline skips them.
    timeline = [
        {"round_num": 1, "total_actions": 5, "active_agents_count": 3,
         "action_types": {"LIKE_POST": 4, "REPOST": 1}},
        {"round_num": 3, "total_actions": 2, "active_agents_count": 2,
         "action_types": {"DO_NOTHING": 2}},
        {"round_num": 5, "total_actions": 9, "active_agents_count": 4,
         "action_types": {"CREATE_POST": 9}},
    ]
    kpis = CampaignKPIs(simulation_id="t")
    CampaignMetricsService._fill_round_metrics(kpis, timeline)

    assert [r["round_num"] for r in kpis.per_round] == [1, 2, 3, 4, 5]
    assert [r["total_actions"] for r in kpis.per_round] == [5, 0, 2, 0, 9]
    assert kpis.peak_round == 5


def test_curve_is_not_capped_but_the_text_is():
    timeline = [
        {"round_num": n, "total_actions": 1, "active_agents_count": 1,
         "action_types": {"LIKE_POST": 1}}
        for n in range(1, 51)
    ]
    kpis = CampaignKPIs(simulation_id="t")
    CampaignMetricsService._fill_round_metrics(kpis, timeline)

    assert len(kpis.per_round) == 50
    rendered = kpis.to_text()
    assert "- R40:" in rendered
    assert "- R41:" not in rendered
