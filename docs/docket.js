/* Docket: reads the data tools/cms_export.py writes, and nothing else.
 *
 * No number is computed here that the exporter could have computed instead,
 * and no refusal is worded here: the sentences come from meta.text, which is
 * cms_ledger's own. What this file does is look things up and lay them out.
 */
"use strict";

const Docket = (() => {
  let meta = null, index = null;
  const stateCache = {};

  async function getJSON(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(path + " returned " + r.status);
    return r.json();
  }

  async function load(base) {
    base = base || "data/";
    [meta, index] = await Promise.all([getJSON(base + "meta.json"),
                                       getJSON(base + "index.json")]);
    Docket.base = base;
    return meta;
  }

  async function state(code) {
    code = String(code).toUpperCase();
    if (!stateCache[code]) {
      stateCache[code] = getJSON(Docket.base + "states/" + code + ".json");
    }
    return stateCache[code];
  }

  /* CCNs are six characters and lead with zeros a spreadsheet eats -
     the same rule as cms_ledger.normalise_ccn. */
  function normaliseCcn(text) {
    text = String(text || "").trim();
    return /^\d+$/.test(text) ? text.padStart(6, "0") : text.toUpperCase();
  }

  function row(entry) {
    return { ccn: entry[0], name: entry[1], city: entry[2],
             state: entry[3], penalised: !!entry[4] };
  }

  /* Name substring, or an exact CCN. Same matching as cms_ledger.search. */
  function search(text, stateCode, limit) {
    const q = String(text || "").trim().toLowerCase();
    const st = (stateCode || "").toUpperCase();
    if (!q && !st) return { rows: [], total: 0 };
    const ccn = /^[0-9a-z]{4,6}$/i.test(q) ? normaliseCcn(q) : null;
    const out = [];
    for (const e of index) {
      if (st && e[3] !== st) continue;
      if (q && !(e[1].toLowerCase().includes(q) || e[0] === ccn)) continue;
      out.push(row(e));
    }
    return { rows: out.slice(0, limit || 50), total: out.length };
  }

  function find(ccn) {
    ccn = normaliseCcn(ccn);
    const e = index.find((x) => x[0] === ccn);
    return e ? row(e) : null;
  }

  async function facility(ccn) {
    const head = find(ccn);
    if (!head) return null;
    const records = await state(head.state);
    const rec = records[head.ccn] || { surveys: [], fines: [], denials: [],
                                       dropped: [] };
    const cols = meta.survey_columns;
    return Object.assign({}, head, {
      fines: rec.fines.map((f) => ({ date: f[0], amount: f[1] })),
      denials: rec.denials.map((d) => ({ date: d[0], start: d[1], days: d[2] })),
      dropped: rec.dropped.map((d) => ({ kind: d[0], date: d[1], value: d[2],
                                         lastSeen: d[3] })),
      surveys: rec.surveys.map((s) => {
        const o = {};
        cols.forEach((c, i) => { o[c] = s[i]; });
        return o;
      }),
      fineTotal: rec.fines.reduce((t, f) => t + (f[1] || 0), 0),
    });
  }

  /* Largest fine totals in a state. Fines only - a denial is measured in
     days and is never folded into a dollar figure. */
  async function largestFines(code, n) {
    const records = await state(code);
    const out = [];
    for (const ccn of Object.keys(records)) {
      const total = records[ccn].fines.reduce((t, f) => t + (f[1] || 0), 0);
      if (total > 0) out.push(Object.assign(find(ccn) || { ccn }, { total }));
    }
    out.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
    return out.slice(0, n || 10);
  }

  return { load, state, search, find, facility, largestFines, normaliseCcn,
           get meta() { return meta; } };
})();

if (typeof module !== "undefined") module.exports = Docket;
