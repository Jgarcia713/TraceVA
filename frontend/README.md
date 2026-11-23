# TraceVA frontend (prototype)

React + TypeScript (Vite), Plotly for charts, React Flow for the provenance graph. A rough draft for the interim presentation, built on the existing backend API.

## Run

```powershell
npm install
npx vite            # http://localhost:5173 (proxies /api to http://127.0.0.1:8000)
```

The backend must run on port 8000. For the demo with recorded model responses, use `..\scripts\start-demo.ps1`, which sets up a separate demo database and starts both servers.

## What it shows

| Panel | Content |
|---|---|
| Conversation | Questions; live progress (each tool call as it is recorded, including failures); answers with the explanation marked **not verified**, numbers not tied to a claim, caveats, and claims as selectable cards. In demo mode, chips list the recorded questions. |
| Claim and evidence | The selected claim with its **agent-proposed citation**. Charts are computed by the backend from the cited evidence; single-value results across views are merged into one comparison. Also: cited evidence with the computation that produced it (tool, arguments, view filters), raw results on demand, assumptions (stated for the claim / for the whole answer / backend method caveats via lineage, all marked *not tested*), and the full operation trace including repaired failures. |
| Provenance graph | The computed graph for the run. The selected claim's lineage is highlighted; edges are styled by origin (backend-verified, agent-proposed citation, assumption). A timeline slider requests snapshots from the backend (`at_sequence`). |

## Conventions

- The frontend computes no statistics; chart specs and every number come from the backend.
- Types in `src/api.ts` mirror `backend/app/schemas/api.py`.
- Checks: `npx tsc -b --noEmit`, `npx vite build`.
