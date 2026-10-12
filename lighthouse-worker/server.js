import express from "express";
import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";
import { randomBytes } from "node:crypto";
import { S3Client, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const app = express();
app.use(express.json());

const PORT = parseInt(process.env.PORT || "3000", 10);
const MAX_CONCURRENT_AUDITS = parseInt(process.env.MAX_CONCURRENT_AUDITS || "2", 10);
const AUDIT_TIMEOUT_MS = parseInt(process.env.AUDIT_TIMEOUT_MS || "60000", 10);
const CHROME_PATH = process.env.CHROME_PATH || "/usr/bin/chromium";
const API_KEY = process.env.API_KEY || null;
const REPORTS_BUCKET = process.env.LIGHTHOUSE_REPORTS_BUCKET || null;
const REPORTS_REGION = process.env.AWS_REGION || "ap-south-1";
const REPORT_URL_TTL_SEC = parseInt(process.env.REPORT_URL_TTL_SEC || "604800", 10);
const s3 = REPORTS_BUCKET ? new S3Client({ region: REPORTS_REGION }) : null;

// Save the full JSON + HTML report to our private S3 bucket and return keys + presigned preview links.
// Never throws: an S3 problem must not break the audit response.
async function saveReports(lhr, report, owner) {
  if (!s3) return null;
  try {
    const safeOwner = String(owner || "shared").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 40) || "shared";
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const base = `lighthouse/${safeOwner}/${stamp}-${randomBytes(4).toString("hex")}`;
    const [jsonBody, htmlBody] = Array.isArray(report) ? report : [JSON.stringify(lhr), null];
    const out = {};
    for (const [ext, body, type] of [["json", jsonBody, "application/json"], ["html", htmlBody, "text/html; charset=utf-8"]]) {
      if (!body) continue;
      const Key = `${base}.${ext}`;
      await s3.send(new PutObjectCommand({ Bucket: REPORTS_BUCKET, Key, Body: body, ContentType: type }));
      out[`${ext}_s3_key`] = Key;
      out[`${ext}_preview_url`] = await getSignedUrl(s3, new GetObjectCommand({ Bucket: REPORTS_BUCKET, Key }), { expiresIn: REPORT_URL_TTL_SEC });
    }
    out.report_url_expires_in_sec = REPORT_URL_TTL_SEC;
    return out;
  } catch (err) {
    console.error("[s3] Failed to save report:", err.message);
    return { report_error: err.message };
  }
}

// Optional API Key authentication middleware (if API_KEY is set)
app.use((req, res, next) => {
  if (req.path === "/health") return next();
  if (!API_KEY) return next();

  const authHeader = req.headers["x-api-key"] || req.headers["authorization"];
  const token = authHeader?.startsWith("Bearer ") ? authHeader.slice(7).trim() : authHeader?.trim();

  if (token !== API_KEY) {
    return res.status(401).json({
      load_success: false,
      error: "Unauthorized: Invalid or missing API key"
    });
  }
  next();
});

// Simple in-memory concurrency semaphore to protect VPS RAM (target 2-3 simultaneous Chrome instances)
let activeAudits = 0;
const waitQueue = [];

function acquireSlot() {
  return new Promise((resolve) => {
    if (activeAudits < MAX_CONCURRENT_AUDITS) {
      activeAudits++;
      resolve();
    } else {
      waitQueue.push(resolve);
    }
  });
}

function releaseSlot() {
  if (waitQueue.length > 0) {
    const next = waitQueue.shift();
    next();
  } else {
    activeAudits = Math.max(0, activeAudits - 1);
  }
}

// Health check endpoint
app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    service: "lighthouse-worker",
    active_audits: activeAudits,
    queued_requests: waitQueue.length,
    max_concurrent: MAX_CONCURRENT_AUDITS
  });
});

// Extract primary document HTTP status code from Lighthouse network audit
function extractHttpStatus(lhr, targetUrl) {
  if (!lhr) return null;
  const requests = lhr.audits?.["network-requests"]?.details?.items;
  if (!Array.isArray(requests) || requests.length === 0) {
    return null;
  }

  // 1. Try Document request matching final displayed URL or target URL
  const docReq = requests.find(
    (r) =>
      r.resourceType === "Document" &&
      (r.url === lhr.finalDisplayedUrl ||
        r.url === lhr.mainDocumentUrl ||
        r.url === targetUrl)
  );
  if (docReq && typeof docReq.statusCode === "number" && docReq.statusCode > 0) {
    return docReq.statusCode;
  }

  // 2. Try any Document request
  const anyDoc = requests.find(
    (r) => r.resourceType === "Document" && typeof r.statusCode === "number" && r.statusCode > 0
  );
  if (anyDoc) {
    return anyDoc.statusCode;
  }

  // 3. Fallback to first network request with a valid status code
  const firstWithStatus = requests.find(
    (r) => typeof r.statusCode === "number" && r.statusCode > 0
  );
  return firstWithStatus ? firstWithStatus.statusCode : null;
}

// Format score to numeric out of 100 with 2 decimals
function formatScore(score) {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return null;
  }
  return Number((score * 100).toFixed(2));
}

// Format integer millisecond metrics
function formatIntMetric(value) {
  return typeof value === "number" && !Number.isNaN(value) ? Math.round(value) : null;
}

// Format CLS metric with 4 decimals
function formatCls(value) {
  return typeof value === "number" && !Number.isNaN(value)
    ? Number(value.toFixed(4))
    : null;
}

// Run single audit with timeout protection
async function runLighthouseAudit(targetUrl) {
  let chrome;

  try {
    chrome = await launch({
      chromePath: CHROME_PATH,
      chromeFlags: [
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer"
      ],
      logLevel: "error"
    });

    const auditPromise = lighthouse(targetUrl, {
      port: chrome.port,
      output: REPORTS_BUCKET ? ["json", "html"] : "json",
      logLevel: "error",
      maxWaitForLoad: 35000,
      onlyCategories: ["performance", "accessibility", "best-practices", "seo"]
    });

    const timeoutPromise = new Promise((_, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Lighthouse audit timed out after ${AUDIT_TIMEOUT_MS}ms`));
      }, AUDIT_TIMEOUT_MS);
      // Ensure timer does not prevent process exit
      if (timer.unref) timer.unref();
    });

    const result = await Promise.race([auditPromise, timeoutPromise]);
    return result;
  } finally {
    if (chrome) {
      try {
        await chrome.kill();
      } catch (err) {
        console.error("Failed to kill Chrome process:", err.message);
      }
    }
  }
}

// Audit POST endpoint
app.post("/audit", async (req, res) => {
  const { url } = req.body || {};

  if (!url || typeof url !== "string" || !url.trim()) {
    return res.status(400).json({
      load_success: false,
      error: "A valid URL is required in request body: { \"url\": \"https://...\" }"
    });
  }

  let formattedUrl = url.trim();
  // Strip accidental leading/trailing '=', quotes, or spaces often introduced by expression editors like n8n
  formattedUrl = formattedUrl.replace(/^[=\s"']+/, "").replace(/[=\s"']+$/, "");

  if (!/^https?:\/\//i.test(formattedUrl)) {
    formattedUrl = `https://${formattedUrl}`;
  }

  try {
    new URL(formattedUrl);
  } catch {
    return res.status(400).json({
      url: formattedUrl,
      load_success: false,
      error: "Invalid URL format provided"
    });
  }

  const startTime = Date.now();
  console.log(`[queue] Waiting for slot to audit: ${formattedUrl} (active: ${activeAudits}, queued: ${waitQueue.length})`);
  await acquireSlot();

  try {
    console.log(`[audit] Starting Lighthouse audit for: ${formattedUrl}`);
    const result = await runLighthouseAudit(formattedUrl);
    const lhr = result?.lhr;

    if (!lhr) {
      throw new Error("Lighthouse returned no report object");
    }

    const httpStatus = extractHttpStatus(lhr, formattedUrl);

    // Check if page failed to load or runtime error occurred (e.g. DNS failure, interstitial, 404, etc.)
    if (lhr.runtimeError) {
      const errorMsg = lhr.runtimeError.message || lhr.runtimeError.code || "Page failed to load";
      console.warn(`[audit] Page load error for ${formattedUrl}: ${errorMsg}`);

      return res.json({
        url: formattedUrl,
        lighthouse_version: lhr.lighthouseVersion || null,
        scores: {
          performance: null,
          accessibility: null,
          best_practices: null,
          seo: null
        },
        metrics: {
          first_contentful_paint_ms: null,
          largest_contentful_paint_ms: null,
          cumulative_layout_shift: null,
          total_blocking_time_ms: null,
          speed_index_ms: null
        },
        load_success: false,
        http_status: httpStatus,
        error: errorMsg
      });
    }

    const durationSec = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`[audit] Audit succeeded for ${formattedUrl} in ${durationSec}s`);

    const reports = await saveReports(lhr, result.report, req.body?.owner);

    return res.json({
      ...(reports ? { reports } : {}),
      url: formattedUrl,
      lighthouse_version: lhr.lighthouseVersion || null,
      scores: {
        performance: formatScore(lhr.categories?.performance?.score),
        accessibility: formatScore(lhr.categories?.accessibility?.score),
        best_practices: formatScore(lhr.categories?.["best-practices"]?.score),
        seo: formatScore(lhr.categories?.seo?.score)
      },
      metrics: {
        first_contentful_paint_ms: formatIntMetric(
          lhr.audits?.["first-contentful-paint"]?.numericValue
        ),
        largest_contentful_paint_ms: formatIntMetric(
          lhr.audits?.["largest-contentful-paint"]?.numericValue
        ),
        cumulative_layout_shift: formatCls(
          lhr.audits?.["cumulative-layout-shift"]?.numericValue
        ),
        total_blocking_time_ms: formatIntMetric(
          lhr.audits?.["total-blocking-time"]?.numericValue
        ),
        speed_index_ms: formatIntMetric(
          lhr.audits?.["speed-index"]?.numericValue
        )
      },
      load_success: true,
      http_status: httpStatus || 200
    });

  } catch (error) {
    const durationSec = ((Date.now() - startTime) / 1000).toFixed(1);
    console.error(`[audit] Audit failed for ${formattedUrl} after ${durationSec}s:`, error.message);

    // Return structured failure response with 200 so orchestrator (n8n) receives structured error payload
    return res.json({
      url: formattedUrl,
      lighthouse_version: null,
      scores: {
        performance: null,
        accessibility: null,
        best_practices: null,
        seo: null
      },
      metrics: {
        first_contentful_paint_ms: null,
        largest_contentful_paint_ms: null,
        cumulative_layout_shift: null,
        total_blocking_time_ms: null,
        speed_index_ms: null
      },
      load_success: false,
      http_status: null,
      error: error.message || "Unknown error during audit"
    });

  } finally {
    releaseSlot();
  }
});

const server = app.listen(PORT, "0.0.0.0", () => {
  console.log(`Lighthouse worker running on port ${PORT} (max concurrent audits: ${MAX_CONCURRENT_AUDITS})`);
});

// Graceful shutdown
function gracefulShutdown(signal) {
  console.log(`Received ${signal}. Shutting down Lighthouse worker gracefully...`);
  server.close(() => {
    console.log("HTTP server closed.");
    process.exit(0);
  });
}

process.on("SIGTERM", () => gracefulShutdown("SIGTERM"));
process.on("SIGINT", () => gracefulShutdown("SIGINT"));