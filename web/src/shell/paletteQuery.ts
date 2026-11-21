export type PaletteScope = "claims" | "agents";

export function paletteQuery(query: string): { scope?: PaletteScope; text: string } {
  const text = query.trim();
  switch (text[0]) {
    case "#":
      return { scope: "claims", text: text.slice(1).trimStart() };
    case "@":
      return { scope: "agents", text: text.slice(1).trimStart() };
    default:
      return { text };
  }
}
