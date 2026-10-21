import type { ChartSpec } from "../api/types";
import { buildOption } from "./buildOption";

const data = { columns: ["region", "total"], rows: [["East", 10], ["West", 20]] };
const spec = (over: Partial<ChartSpec>): ChartSpec => ({ type: "bar", title: "t", x: "region", y: ["total"], series: null, data, ...over });

describe("buildOption", () => {
  it("builds a single-series bar chart", () => {
    const o = buildOption(spec({})) as any;
    expect(o.xAxis.data).toEqual(["East", "West"]);
    expect(o.series).toHaveLength(1);
    expect(o.series[0]).toMatchObject({ type: "bar", name: "total", data: [10, 20] });
  });

  it("pivots rows into one series per category", () => {
    const rows = [["Jan", "East", 1], ["Jan", "West", 2], ["Feb", "East", 3]];
    const o = buildOption(spec({ type: "line", x: "month", y: ["v"], series: "region", data: { columns: ["month", "region", "v"], rows } })) as any;
    expect(o.xAxis.data).toEqual(["Jan", "Feb"]);
    expect(o.series.map((s: any) => [s.name, s.data])).toEqual([["East", [1, 3]], ["West", [2, null]]]);
    expect(o.legend).toBeDefined();
  });

  it("builds pie and area charts", () => {
    const pie = buildOption(spec({ type: "pie" })) as any;
    expect(pie.series[0].data).toEqual([{ name: "East", value: 10 }, { name: "West", value: 20 }]);
    const area = buildOption(spec({ type: "area" })) as any;
    expect(area.series[0].type).toBe("line");
    expect(area.series[0].areaStyle).toEqual({});
  });

  it("returns null for tables, KPIs and unknown columns", () => {
    expect(buildOption(spec({ type: "table" }))).toBeNull();
    expect(buildOption(spec({ type: "kpi" }))).toBeNull();
    expect(buildOption(spec({ x: "nope" }))).toBeNull();
    expect(buildOption(spec({ y: ["nope"] }))).toBeNull();
  });
});
