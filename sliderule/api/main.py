"""FastAPI app. Routes validate, write, enqueue and return — no model calls,
agent runs or external API calls inside a request; the worker executes."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from sliderule.api.auth import AuthContext, ClerkVerifier, TokenVerifier, get_auth
from sliderule.transition import GRAPH


def create_app(token_verifier: TokenVerifier | None = None) -> FastAPI:
    app = FastAPI(title="sliderule")
    app.state.token_verifier = token_verifier or ClerkVerifier()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/stages")
    def stages(auth: AuthContext = Depends(get_auth)) -> dict:
        """The transition graph, straight from the state machine — the
        dashboard draws the board from the same source that enforces it."""
        return {
            "graph": {
                from_stage: {to: sorted(actors) for to, actors in edges.items()}
                for from_stage, edges in GRAPH.items()
            }
        }

    return app


app = create_app()
