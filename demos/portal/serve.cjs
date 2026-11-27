const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
const routes = {'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css'};
const types = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'};
const server = http.createServer((req,res)=>{
  const file = routes[new URL(req.url,'http://localhost').pathname];
  if(!file){res.writeHead(404);res.end('Not found');return;}
  fs.readFile(path.join(root,file),(error,data)=>{
    if(error){res.writeHead(500);res.end('Preview unavailable');return;}
    res.writeHead(200,{'Content-Type':types[path.extname(file)],'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'none'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"});res.end(data);
  });
});
server.listen(4173,'127.0.0.1',()=>console.log('Portal preview: http://127.0.0.1:4173'));
server.on('error',error=>{console.error(error.message);process.exitCode=1});
