import express from "express";
import cors from "cors";
import pg from "pg";
import fs from "fs/promises";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, ".env") });

const { Pool } = pg;

const pool = new Pool({
  host: process.env.PG_HOST,
  port: Number(process.env.PG_PORT || 5432),
  database: process.env.PG_DATABASE,
  user: process.env.PG_USER,
  password: process.env.PG_PASSWORD,
  ssl: process.env.PG_SSLMODE === "require" ? { rejectUnauthorized: false } : undefined,
});

const app = express();
app.use(cors());
app.use(express.json());

// Read-only: fetch all ticket_records rows (no embedding column — large vectors not needed by the UI).
app.get("/api/tickets", async (_req, res) => {
  try {
    const { rows } = await pool.query(`
      SELECT
        record_id,
        source_ref_id,
        module,
        category,
        severity,
        resolution_type,
        title,
        raw_text,
        structured_sop,
        guidewire_version,
        event_date,
        source_metadata,
        curation_status,
        curation_notes,
        kb_version,
        ingested_at
      FROM ticket_records
      ORDER BY (source_metadata ->> 'created_at')::timestamp DESC NULLS LAST
    `);
    res.json(rows);
  } catch (err) {
    console.error("GET /api/tickets failed:", err.message);
    res.status(500).json({ error: "Failed to fetch tickets from database." });
  }
});

// Distinct modules actually present in ticket_records (real data, not a hardcoded list).
app.get("/api/modules", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT DISTINCT module FROM ticket_records ORDER BY module`,
    );
    res.json(rows.map((r) => r.module));
  } catch (err) {
    console.error("GET /api/modules failed:", err.message);
    res.status(500).json({ error: "Failed to fetch modules." });
  }
});

// Assignee teams pulled live from the backend's raw ticket sample JSON (read-only file read).
app.get("/api/assignee-teams", async (_req, res) => {
  try {
    const raw = await fs.readFile(process.env.RAW_TICKETS_JSON, "utf-8");
    const tickets = JSON.parse(raw);
    const teams = Array.from(new Set(tickets.map((t) => t.assignee_team))).sort();
    res.json(teams);
  } catch (err) {
    console.error("GET /api/assignee-teams failed:", err.message);
    res.status(500).json({ error: "Failed to read assignee teams from raw sample JSON." });
  }
});

const port = Number(process.env.PORT || 4000);
app.listen(port, () => {
  console.log(`Read-only API server listening on http://localhost:${port}`);
});
