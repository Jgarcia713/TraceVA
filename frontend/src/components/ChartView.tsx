import { useEffect, useRef } from "react";
import type { ChartSpec } from "../api";
import { shortId } from "../format";

const PALETTE = ["#3b6fd8", "#e08a2c", "#2f9e72", "#b5487f", "#7a5fc9", "#8a8f98"];

type PlotlyModule = typeof import("plotly.js-basic-dist-min").default;
let plotlyPromise: Promise<PlotlyModule> | null = null;

/** Plotly is ~1 MB: load it only when the first chart renders (code splitting). */
function loadPlotly(): Promise<PlotlyModule> {
  plotlyPromise ??= import("plotly.js-basic-dist-min").then((m) => m.default);
  return plotlyPromise;
}

function formatValue(v: number | null, percent: boolean): string {
  if (v === null) return "–";
  return percent ? `${(v * 100).toFixed(1)}%` : v.toFixed(2);
}

/** Renders a backend chart spec. The frontend only maps the spec to Plotly; it never
 *  computes values (charts are derived from evidence on the server). */
export function ChartView({ spec }: { spec: ChartSpec }) {
  const ref = useRef<HTMLDivElement>(null);
  const percent = spec.y_format === "percent";

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let cancelled = false;
    const traces = spec.series.map((s, i) => ({
      type: "bar",
      name: s.name,
      x: s.x.map((v) => (v === null ? "(missing)" : String(v))),
      y: s.y,
      customdata: s.n,
      marker: { color: PALETTE[i % PALETTE.length] },
      hovertemplate: `%{x}<br>${percent ? "%{y:.1%}" : "%{y:.2f}"}<br>n = %{customdata:,}<extra>${s.name}</extra>`,
      text: s.y.map((v) => (v === null ? "" : formatValue(v, percent))),
      textposition: spec.barmode === "stack" ? "inside" : "outside",
      cliponaxis: false,
    }));
    void loadPlotly().then((Plotly) => {
      if (cancelled) return;
      void Plotly.react(
        el,
        traces,
        {
          barmode: spec.barmode,
          margin: { t: 12, r: 12, b: 70, l: 56 },
          height: 300,
          font: { family: "Inter, system-ui, sans-serif", size: 12, color: "#1f2733" },
          xaxis: {
            title: { text: spec.x_label },
            automargin: true,
            tickangle: spec.series[0]?.x.length > 6 ? -35 : 0,
          },
          yaxis: { title: { text: spec.y_label }, tickformat: percent ? ".0%" : undefined, rangemode: "tozero" },
          showlegend: spec.series.length > 1,
          legend: { orientation: "h", y: 1.12 },
          paper_bgcolor: "rgba(0,0,0,0)",
          plot_bgcolor: "rgba(0,0,0,0)",
        },
        { displayModeBar: false, responsive: true },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [spec, percent]);

  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el) void loadPlotly().then((Plotly) => Plotly.purge(el));
    };
  }, []);

  const rows = spec.series[0]?.x ?? [];
  return (
    <figure className="chart">
      <figcaption className="chart-title">{spec.title}</figcaption>
      {/* The plot is visual; the data table below is its accessible alternative. */}
      <div ref={ref} aria-hidden="true" />
      <details className="chart-data">
        <summary>Data table</summary>
        <table>
          <caption className="visually-hidden">{spec.title}</caption>
          <thead>
            <tr>
              <th scope="col">{spec.x_label || "group"}</th>
              {spec.series.map((s) => (
                <th key={s.name} scope="col">
                  {s.name}
                </th>
              ))}
              <th scope="col">n</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x, i) => (
              <tr key={`${String(x)}-${i}`}>
                <th scope="row">{x === null ? "(missing)" : String(x)}</th>
                {spec.series.map((s) => (
                  <td key={s.name}>{formatValue(s.y[i] ?? null, percent)}</td>
                ))}
                <td>{spec.series[0].n[i]?.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <div className="chart-meta">
        <span className="badge badge-backend">backend-computed</span>
        <span>
          from evidence{" "}
          {spec.evidence_id.split(",").map((id) => (
            <code key={id}>{shortId(id)} </code>
          ))}
        </span>
        {spec.notes.map((note) => (
          <span key={note} className="chart-note">
            {note}
          </span>
        ))}
      </div>
    </figure>
  );
}
