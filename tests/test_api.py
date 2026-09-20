"""API skeleton tests. The token verifier is injected — no network, no Clerk."""

from fastapi.testclient import TestClient

from sliderule.api.auth import AuthContext, InvalidToken
from sliderule.api.main import create_app
from sliderule.transition import GRAPH


class FakeVerifier:
    def verify(self, token: str) -> AuthContext:
        if token != "good-token":
            raise InvalidToken("bad signature")
        return AuthContext(
            clerk_user_id="user_1", clerk_org_id="org_concord", role="admin"
        )


def client() -> TestClient:
    return TestClient(create_app(token_verifier=FakeVerifier()))


def test_health_needs_no_auth():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_stages_without_token_is_401():
    assert client().get("/stages").status_code == 401


def test_stages_with_bad_token_is_401():
    response = client().get(
        "/stages", headers={"Authorization": "Bearer forged"}
    )
    assert response.status_code == 401


def test_stages_serves_the_transition_graph():
    response = client().get(
        "/stages", headers={"Authorization": "Bearer good-token"}
    )
    assert response.status_code == 200
    graph = response.json()["graph"]
    assert set(graph) == set(GRAPH)
    for from_stage, edges in GRAPH.items():
        assert graph[from_stage] == {
            to: sorted(actors) for to, actors in edges.items()
        }
    # spot-check the rules the graph must encode
    assert graph["screened"]["approved"] == ["human"]
    assert "replied" in graph["contacted"]
    assert graph["opted_out"] == {}
