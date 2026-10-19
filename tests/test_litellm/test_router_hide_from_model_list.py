"""
model_info.hide_from_model_list: true hides a model group from client listings
(/v1/models) while it stays callable by name.
"""

import litellm


def _router(model_list):
    return litellm.Router(model_list=model_list)


def _dep(name, **model_info):
    return {"model_name": name, "litellm_params": {"model": f"openai/{name}", "api_key": "x"}, "model_info": model_info}


def test_flagged_groups_are_hidden_and_others_listed():
    r = _router([
        _dep("chat-a"),
        _dep("img-a", mode="image_generation", hide_from_model_list=True),
        _dep("old-chat", hide_from_model_list=True),
        _dep("x-fallback"),
    ])
    assert r.get_listing_hidden_model_names() == {"img-a", "old-chat", "x-fallback"}


def test_group_stays_listed_unless_every_deployment_opts_in():
    r = _router([_dep("mixed", hide_from_model_list=True), _dep("mixed")])
    assert "mixed" not in r.get_listing_hidden_model_names()


def test_only_literal_true_hides():
    r = _router([_dep("a", hide_from_model_list="yes"), _dep("b", hide_from_model_list=False)])
    assert r.get_listing_hidden_model_names() == set()


def test_hidden_model_is_still_routable():
    r = _router([_dep("old-chat", hide_from_model_list=True)])
    resp = r.completion(model="old-chat", messages=[{"role": "user", "content": "hi"}], mock_response="ok")
    assert resp.choices[0].message.content == "ok"
