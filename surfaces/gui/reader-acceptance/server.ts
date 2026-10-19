import {spawn, execFileSync, type ChildProcess} from "node:child_process";
import {mkdtemp, mkdir, writeFile, rm} from "node:fs/promises";
import {createWriteStream} from "node:fs";
import {tmpdir} from "node:os";
import path from "node:path";
import {once} from "node:events";
import {createServer} from "node:net";
import {setTimeout as delay} from "node:timers/promises";
import {expect, type APIRequestContext} from "@playwright/test";

export const root = path.resolve("../..");
export const login = {username: "reader-acceptance", password: "isolated-reader-password"};
export async function startServer(request: APIRequestContext, output: string) {
  const caddy = process.env.EDISON_CADDY_BIN || "caddy";
  execFileSync(caddy,["version"],{stdio:"ignore"});
  // Refuse occupied ports before starting: never target another running instance.
  for(const port of [8792,8943]) {
    const probe=createServer();
    await new Promise<void>((resolve,reject)=>{probe.once("error",reject);probe.listen(port,"127.0.0.1",()=>probe.close(error=>error?reject(error):resolve()));});
  }
  const dir = await mkdtemp(path.join(tmpdir(), "edison-reader-acceptance-"));
  await mkdir(output, {recursive:true});
  await mkdir(path.join(dir,"state"));
  const logs: ReturnType<typeof createWriteStream>[] = [];
  const env: NodeJS.ProcessEnv = {
    PATH: process.env.PATH, HOME: process.env.HOME, LANG: "en_US.UTF-8",
    PYTHONPATH: root, COWORKER_STATE_DIR: path.join(dir,"state"),
    COWORKER_SCRATCH_BASE: path.join(dir,"scratch"),
    COWORKER_API_TOKEN: "isolated-proxy-token", EDISON_WEB_LOGIN: "1",
    EDISON_WEB_USERNAME: login.username,
    EDISON_WEB_PASSWORD_HASH_FILE: path.join(dir,"password.hash"),
    XDG_DATA_HOME: path.join(dir,"xdg-data"), XDG_CONFIG_HOME: path.join(dir,"xdg-config"),
  };
  const python = path.join(root,".venv/bin/python");
  const hash = execFileSync(python,["-c","import bcrypt; print(bcrypt.hashpw(b'isolated-reader-password', bcrypt.gensalt()).decode())"],{env}).toString();
  await writeFile(env.EDISON_WEB_PASSWORD_HASH_FILE!,hash,{mode:0o600});
  execFileSync("openssl",["req","-x509","-newkey","rsa:2048","-nodes","-days","1","-keyout",path.join(dir,"key.pem"),"-out",path.join(dir,"cert.pem"),"-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost,IP:127.0.0.1"],{stdio:"ignore"});
  const caddyfile=path.join(dir,"Caddyfile");
  await writeFile(caddyfile,`{
  admin off
  auto_https off
}
https://localhost:8943 {
  bind 127.0.0.1
  tls ${dir}/cert.pem ${dir}/key.pem
  @sameOrigin header Origin https://localhost:8943
  request_header @sameOrigin Origin http://localhost
  @login path /login /v1/web-auth/login /v1/web-auth/logout
  handle @login {
    reverse_proxy 127.0.0.1:8792
  }
  handle {
    forward_auth 127.0.0.1:8792 {
      uri /v1/web-auth/check
    }
    @api path /v1/* /ws/*
    handle @api {
      reverse_proxy 127.0.0.1:8792 {
        header_up X-OpenWorker-Token isolated-proxy-token
      }
    }
    handle {
      root * ${root}/surfaces/gui/dist
      try_files {path} /index.html
      file_server
    }
  }
}
`);
  function launch(command:string,args:string[],name:string) {
    const log=createWriteStream(path.join(output,name),{flags:"a"});logs.push(log);
    const child=spawn(command,args,{cwd:dir,env,stdio:["ignore","pipe","pipe"]});
    child.stdout!.pipe(log,{end:false}); child.stderr!.pipe(log,{end:false});
    return child;
  }
  async function stop(child:ChildProcess|undefined,signal:NodeJS.Signals="SIGTERM") {
    if(!child || child.exitCode!==null || child.signalCode!==null)return;
    const ended=once(child,"exit");child.kill(signal);
    await Promise.race([ended,delay(8000).then(()=>{if(child.exitCode===null&&child.signalCode===null)child.kill("SIGKILL");})]);
    if(child.exitCode===null&&child.signalCode===null)await ended;
  }
  let backend:ChildProcess|undefined,proxy:ChildProcess|undefined;
  const startBackend=async()=>{
    backend=launch(python,["-m","coworker.server.run","--host","127.0.0.1","--port","8792","--cwd",dir],"backend.log");
    await expect.poll(async()=>{
      if(backend!.exitCode!==null)throw new Error("Backend exited; see backend.log");
      try{return (await request.get("http://127.0.0.1:8792/v1/health")).status();}catch{return 0;}
    },{timeout:30000}).toBe(200);
  };
  const close=async()=>{await stop(proxy);await stop(backend);for(const log of logs)log.end();await rm(dir,{recursive:true,force:true});};
  try {
    await startBackend();
    proxy=launch(caddy,["run","--config",caddyfile,"--adapter","caddyfile"],"caddy.log");
    await expect.poll(async()=>{
      if(proxy!.exitCode!==null)throw new Error("Caddy exited; see caddy.log");
      try{return (await request.get("https://localhost:8943/login")).status();}catch{return 0;}
    },{timeout:20000}).toBe(200);
    return {close,restart:async()=>{await stop(backend,"SIGKILL");await startBackend();}};
  }catch(error){await close();throw error;}
}
