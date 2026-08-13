// E2E 服务启动器：node 调 venv python（避免 cmd 对中文路径的编码问题）。
// 用法：node e2e-server-launcher.js <fake|app>

const { spawn } = require('child_process');
const path = require('path');

const which = process.argv[2];
const py = path.resolve(__dirname, '..', '.venv', 'Scripts', 'python.exe');
const script = path.resolve(__dirname, which === 'fake' ? 'fake_llm.py' : 'e2e_app.py');
// 清除 Hermes bash 注入的 PYTHONPATH（指向 Hermes venv 的坏包），只用项目 venv
const env = { ...process.env };
delete env.PYTHONPATH;
const child = spawn(py, [script], { stdio: 'inherit', env });
child.on('exit', (code, sig) => {
  if (code !== 0 && sig) process.exit(1);
  process.exit(code === null ? 1 : code);
});
