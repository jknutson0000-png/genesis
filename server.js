const http = require('http');
const path = require('path');
const { readFile } = require('fs/promises');

const HOST = process.env.HOST || '127.0.0.1';
const PORT = Number.parseInt(process.env.PORT || '4173', 10);
const ROOT_DIR = __dirname;
const DEFAULT_FILE = 'index.html';

const MIME_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.webp': 'image/webp'
};

const isValidPort = Number.isInteger(PORT) && PORT > 0 && PORT <= 65535;
if (!isValidPort) {
  throw new Error(`Invalid PORT value: ${process.env.PORT || 'undefined'}`);
}

const getFilePath = (requestUrl = '/') => {
  const pathname = new URL(requestUrl, `http://${HOST}:${PORT}`).pathname;
  const normalizedPath = pathname === '/' ? `/${DEFAULT_FILE}` : pathname;
  const decodedPath = decodeURIComponent(normalizedPath);
  const safePath = path.normalize(decodedPath).replace(/^([.][.][/\\])+/, '');
  const resolvedPath = path.join(ROOT_DIR, safePath);

  if (!resolvedPath.startsWith(ROOT_DIR)) {
    throw new Error('Invalid path traversal attempt detected.');
  }

  return resolvedPath;
};

const getMimeType = (filePath) => MIME_TYPES[path.extname(filePath)] || 'application/octet-stream';

const requestListener = async (request, response) => {
  try {
    const method = request.method || 'GET';
    if (!['GET', 'HEAD'].includes(method)) {
      response.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify({ error: 'Method not allowed' }));
      return;
    }

    const filePath = getFilePath(request.url);
    const fileContents = await readFile(filePath);

    response.writeHead(200, {
      'Content-Type': getMimeType(filePath),
      'Cache-Control': 'no-store'
    });

    if (method === 'HEAD') {
      response.end();
      return;
    }

    response.end(fileContents);
  } catch (error) {
    const statusCode = error.code === 'ENOENT' ? 404 : 500;
    const message = statusCode === 404 ? 'File not found' : 'Unable to load preview';

    response.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify({ error: message }));
  }
};

const server = http.createServer((request, response) => {
  requestListener(request, response).catch((error) => {
    response.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify({ error: 'Unexpected server error' }));
    console.error(error);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Preview available at http://${HOST}:${PORT}`);
});
