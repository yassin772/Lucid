const fs = require("fs");
const path = require("path");

const url = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const anonKey = process.env.SUPABASE_ANON_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

if (!url || !anonKey) {
  console.warn("Supabase public config is missing. Auth pages will show the configuration state.");
}

const output = `window.LUCID_SUPABASE_CONFIG = {
  url: ${JSON.stringify(url)},
  anonKey: ${JSON.stringify(anonKey)}
};
`;

const outputPath = path.join(__dirname, "..", "js", "supabase_config.js");
fs.writeFileSync(outputPath, output, "utf8");
console.log(`Wrote ${path.relative(process.cwd(), outputPath)}`);
