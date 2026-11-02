/** Authenticated, read-only proxy for the taste-skill inspector. QA credentials only. */
import { createServer } from "node:http";
const role = process.argv[2] === "user" ? "user" : "admin";
const port = role === "user" ? 8175 : 8174;
const origin = "http://127.0.0.1:8100";
const login = await fetch(origin + "/api/v1/users/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    username: role === "user" ? "qa_customer" : "qa_admin",
    password: "Local-QA-only-2026!",
  }),
});
if (!login.ok) throw new Error("QA login failed: " + login.status);
const account = (await login.json()).data;
const publicUser = {
  id: account.id,
  username: account.username,
  nickname: account.nickname,
  level: account.level,
  role: account.role,
  status: account.status,
};
const injection =
  '<script>localStorage.setItem("cs_access_token",' +
  JSON.stringify(account.access_token) +
  ');localStorage.setItem("cs_refresh_token",' +
  JSON.stringify(account.refresh_token) +
  ');localStorage.setItem("cs_user",' +
  JSON.stringify(JSON.stringify(publicUser)) +
  ")</script>";
createServer(async (req, res) => {
  try {
    const path = req.url || "/";
    const upstream = await fetch(origin + path, {
      method: req.method,
      headers: {
        authorization: req.headers.authorization || "",
        "content-type": req.headers["content-type"] || "",
      },
    });
    let body = Buffer.from(await upstream.arrayBuffer());
    if (path.startsWith("/?") || path === "/")
      body = Buffer.from(
        body.toString().replace("</head>", injection + "</head>"),
      );
    res.writeHead(upstream.status, {
      "Content-Type":
        upstream.headers.get("content-type") || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    res.end(body);
  } catch (error) {
    res.writeHead(502);
    res.end(String(error));
  }
}).listen(port, "127.0.0.1", () =>
  console.log("QA inspector proxy for " + role + " listening on " + port),
);
