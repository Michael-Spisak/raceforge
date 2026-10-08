import { fieldErrorsFrom } from "./client";

describe("fieldErrorsFrom", () => {
  it("maps FastAPI 422 details to form fields and keeps the engine hint", () => {
    const detail = [{ loc: ["body", "wheelbase_studs"], msg: "Value error, wheelbase_studs=30 is not possible; nearest valid: [20, 21]" }];
    expect(fieldErrorsFrom(detail)).toEqual({ wheelbase_studs: "wheelbase_studs=30 is not possible; nearest valid: [20, 21]" });
  });

  it("attaches model-level '<field>=…' errors to that field", () => {
    const detail = [{ loc: ["body"], msg: "Value error, track_studs=7 is not possible; nearest valid: [9, 10]" }];
    expect(fieldErrorsFrom(detail)).toEqual({ track_studs: "track_studs=7 is not possible; nearest valid: [9, 10]" });
  });

  it("puts other model-level errors under '_' and tolerates junk", () => {
    expect(fieldErrorsFrom([{ loc: ["body"], msg: "bad" }])).toEqual({ _: "bad" });
    expect(fieldErrorsFrom("oops")).toEqual({});
  });
});
