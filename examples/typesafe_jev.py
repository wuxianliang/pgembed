"""pg_typesafe 0.1.0 in pgembed: catalog version, native jev_version, and a mocked classify."""

import tempfile

import pgembed

with tempfile.TemporaryDirectory() as tmpdir:
    with pgembed.get_server(tmpdir) as server:
        assert server.create_extension("pg_typesafe").strip() == "CREATE EXTENSION"
        print(server.psql("SELECT extversion FROM pg_extension WHERE extname = 'typesafe';"))
        print(server.psql("SELECT jev_version();"))
        print(
            server.psql(
                "SELECT comment FROM pg_available_extensions WHERE name = 'typesafe';"
            )
        )
        print(
            server.psql(
                """
SET typesafe.mock_response = $${
  "model": "jev-latest",
  "answers": {"label": {"type": "choice", "choice": "technical",
                        "confidence": 0.82,
                        "probabilities": {"billing": 0.08, "technical": 0.85, "sales": 0.07}}},
  "usage": {"input_tokens": 312, "output_tokens": 48}
}$$;
SELECT choice, confidence FROM typesafe_classify(
    'Help! My payouts have been failing for 3 days.',
    'Which team should handle this?',
    '{"billing": "Payments, invoicing, refunds",
      "technical": "Bugs, outages, integrations",
      "sales": "Pricing, upgrades, new accounts"}'::jsonb);
"""
            )
        )
