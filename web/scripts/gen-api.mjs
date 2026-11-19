import { readFile, writeFile } from "node:fs/promises";
import process from "node:process";
import { URL } from "node:url";

const source = new URL("../../research_multiplier/schemas/workbench.openapi.json", import.meta.url);
const destination = new URL("../src/api/claims.generated.ts", import.meta.url);
const schemas = JSON.parse(await readFile(source, "utf8")).components.schemas;

function scalar(type) {
  if (["string", "boolean", "null"].includes(type)) return type;
  throw new Error(`Unsupported API field type: ${type}`);
}

function fieldType(field) {
  if (field.$ref) {
    const match = /^#\/components\/schemas\/([A-Za-z]+)$/.exec(field.$ref);
    if (match && Object.hasOwn(schemas, match[1])) return match[1];
    throw new Error(`Unsupported API type reference: ${field.$ref}`);
  }
  if (field.type === "array") return `Array<${fieldType(field.items)}>`;
  if (Array.isArray(field.type)) return field.type.map(scalar).join(" | ");
  return scalar(field.type);
}

function declaration(name, schema) {
  if (schema.type !== "object") throw new Error(`Unsupported API type: ${name}`);
  const fields = Object.entries(schema.properties).map(([key, field]) => {
    const optional = schema.required.includes(key) ? "" : "?";
    return `  ${key}${optional}: ${fieldType(field)};`;
  });
  return [`export type ${name} = {`, ...fields, "};"].join("\n");
}

const generated =
  [
    "// Generated from workbench.openapi.json by pnpm gen:api.",
    ...Object.entries(schemas).map(([name, schema]) => declaration(name, schema)),
  ].join("\n\n") + "\n";

if (process.argv[2] === "--check") {
  const current = await readFile(destination, "utf8").catch((error) => {
    if (error.code !== "ENOENT") throw error;
    return null;
  });
  if (current !== generated) {
    process.stderr.write("Workbench API types have changed. Run pnpm gen:api.\n");
    process.exitCode = 1;
  }
} else {
  await writeFile(destination, generated);
}
