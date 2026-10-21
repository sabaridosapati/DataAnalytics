import { toCsv } from "./csv";

describe("toCsv", () => {
  it("escapes quotes, commas and newlines", () => {
    expect(toCsv(["a", "b"], [["x,y", 'say "hi"'], ["line\nbreak", null]])).toBe(
      'a,b\r\n"x,y","say ""hi"""\r\n"line\nbreak",',
    );
  });

  it("neutralizes spreadsheet formulas but keeps numbers", () => {
    expect(toCsv(["v"], [["=SUM(A1)"], ["+cmd"], ["@x"], [-5], ["-3.5"]])).toBe("v\r\n'=SUM(A1)\r\n'+cmd\r\n'@x\r\n-5\r\n-3.5");
  });
});
