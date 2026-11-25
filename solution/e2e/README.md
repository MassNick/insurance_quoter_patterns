# End-to-end tests

Three levels, each with the fake model (`QUOTE_AGENT_MODEL=fake`) and no Azure:

| Suite | What runs | Command | Where |
|---|---|---|---|
| API journeys | BFF, quote-agent, mcp-quoting, mcp-a2ui as local processes, driven over HTTP + SSE | `bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e` | `e2e/test_api_journey.py` |
| Browser, full stack | the same four services, the built web app and Chromium | `cd web && npm run test:stack` | `web/e2e-stack/` |
| Browser, mock BFF | the web app only, BFF scripted (fast, every UI path) | `cd web && npm run test:e2e` | `web/e2e/` |

`e2e/stack.py` starts the stack. Every service runs from its own `.venv` (see each component's README):

```bash
python3 e2e/stack.py up                      # ports 8000, 8088, 8101, 8102 until Ctrl-C
python3 e2e/stack.py up --offset 10000 --web # 18000, ... and the built web app on 15173 (run `npm run build` in web/ first)
```

The pytest fixture starts it on ports +20000 (`E2E_PORT_OFFSET`), the browser suite on +30000, so both can run while
you develop on the standard ports. The stack refuses to start on a busy port, so a stale service never answers
with old code. Each service's log is in `e2e/.logs/<name>.log`.

## In containers (CI)

`e2e/docker-compose.yml` builds the Azure images and runs the browser suite in the Playwright image:

```bash
docker compose -f e2e/docker-compose.yml up --build --exit-code-from e2e
docker compose -f e2e/docker-compose.yml down -v
```

Results: `e2e/test-results/` (traces of failures) and `e2e/playwright-report/`.

## Adding a journey

- Reuse the gestures in `web/e2e/helpers.ts` (`start`, `consent`, `confirmTwice`, `send`, `suggestion`, `form`)
  and `Session` in `test_api_journey.py` (`say`, `submit`, `confirm`, `value`, `surface`).
- The fake model reacts to the words in `agents/quote-agent/src/quote_agent/fake/rules.py`. A journey that needs new
  wording adds it there, with a test in that component.
- Assert on what the customer or the next system sees (text, forms, state values, events), not on timings.
