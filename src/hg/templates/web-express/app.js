const express = require("express");

const app = express();
const port = parseInt(process.env.HG_WEB_PORT || "8000", 10);

app.get("/", (_req, res) => {
  res.type("text/plain").send("hello\n");
});

app.listen(port);
