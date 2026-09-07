// @ts-check
/**
 * Electron Main Logger
 * Appends logs to %APPDATA%\daon-agent-system\daon-main.log
 */
const fs = require('fs');
const path = require('path');
const { app } = require('electron');

let _mainLogStream = null;

function mainLogInit() {
  try {
    const logPath = path.join(app.getPath('userData'), 'daon-main.log');
    _mainLogStream = fs.createWriteStream(logPath, { flags: 'a' });
    _mainLogStream.write(`\n===== Electron main started ${new Date().toISOString()} (pid=${process.pid}) =====\n`);
  } catch (_) { }
}

function mlog(...args) {
  try {
    const line = `[${new Date().toISOString()}] ` + args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
    console.log(line);
    if (_mainLogStream) _mainLogStream.write(line + '\n');
  } catch (_) { }
}

function merr(...args) {
  try {
    const line = `[${new Date().toISOString()}] [ERR] ` + args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
    console.error(line);
    if (_mainLogStream) _mainLogStream.write(line + '\n');
  } catch (_) { }
}

module.exports = {
  mainLogInit,
  mlog,
  merr,
};
