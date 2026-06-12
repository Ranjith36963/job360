/**
 * V-04 — client-side upload guard unit tests.
 *
 * Tests the exported `validateUploadFile` helper from CVUpload.tsx.
 * These run in Vitest without needing a browser or full component render.
 */

import { describe, it, expect } from "vitest";
import { validateUploadFile } from "./CVUpload";

const MB = 1024 * 1024;

function makeFile(name: string, sizeBytes: number): File {
  // File constructor: [parts], name, options
  const blob = new Blob([new Uint8Array(sizeBytes)]);
  return new File([blob], name, { type: "application/octet-stream" });
}

describe("validateUploadFile", () => {
  it("accepts a small PDF", () => {
    const file = makeFile("cv.pdf", 100 * 1024); // 100 KB
    expect(validateUploadFile(file)).toBeNull();
  });

  it("accepts a small DOCX", () => {
    const file = makeFile("resume.docx", 500 * 1024); // 500 KB
    expect(validateUploadFile(file)).toBeNull();
  });

  it("accepts a PDF exactly at the 10 MB limit", () => {
    const file = makeFile("cv.pdf", 10 * MB);
    expect(validateUploadFile(file)).toBeNull();
  });

  it("rejects a file one byte over 10 MB", () => {
    const file = makeFile("cv.pdf", 10 * MB + 1);
    expect(validateUploadFile(file)).toMatch(/too large/i);
  });

  it("rejects a .txt file", () => {
    const file = makeFile("resume.txt", 100);
    expect(validateUploadFile(file)).toMatch(/PDF or DOCX/i);
  });

  it("rejects a .exe file", () => {
    const file = makeFile("bad.exe", 100);
    expect(validateUploadFile(file)).toMatch(/PDF or DOCX/i);
  });

  it("rejects a file with no extension", () => {
    const file = makeFile("noext", 100);
    expect(validateUploadFile(file)).toMatch(/PDF or DOCX/i);
  });

  it("accepts .PDF (uppercase extension)", () => {
    const file = makeFile("CV.PDF", 100 * 1024);
    expect(validateUploadFile(file)).toBeNull();
  });

  it("accepts .DOCX (uppercase extension)", () => {
    const file = makeFile("Resume.DOCX", 100 * 1024);
    expect(validateUploadFile(file)).toBeNull();
  });
});
