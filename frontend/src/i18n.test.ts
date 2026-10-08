import de from "./locales/de.json";
import en from "./locales/en.json";

describe("translations", () => {
  it("have the same keys in German and English", () => {
    expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort());
  });

  it("have no empty strings", () => {
    for (const table of [de, en]) {
      for (const [key, value] of Object.entries(table)) expect(value, key).not.toBe("");
    }
  });
});
