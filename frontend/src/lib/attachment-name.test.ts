import { describe, expect, it } from "vitest";
import { attachmentName } from "./api";

describe("attachmentName - the download's own file name, shown in the CV preview title", () => {
  it("reads the plain and the encoded forms; none gives empty", () => {
    expect(attachmentName('attachment; filename="Ranjith_Guruprakash_CV.pdf"')).toBe("Ranjith_Guruprakash_CV.pdf");
    expect(attachmentName("attachment; filename*=UTF-8''Ren%C3%A9_CV.pdf")).toBe("René_CV.pdf");
    expect(attachmentName("attachment")).toBe("");
    expect(attachmentName(null)).toBe("");
  });
});
