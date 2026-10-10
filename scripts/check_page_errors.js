const puppeteer = require('puppeteer-core');
const fs = require('fs');

async function test() {
    // find chrome path
    const chromePaths = [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
        'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
    ];
    let execPath = null;
    for (const p of chromePaths) {
        if (fs.existsSync(p)) {
            execPath = p;
            break;
        }
    }
    console.log('Using browser:', execPath);
    
    // Check if puppeteer is importable from node_modules
    let pptr;
    try {
        pptr = require('puppeteer');
    } catch(e) {
        pptr = require('C:\\Users\\ttl09\\AppData\\Roaming\\npm\\node_modules\\@modelcontextprotocol\\server-puppeteer\\node_modules\\puppeteer');
    }
    
    const browser = await pptr.launch({
        executablePath: execPath,
        headless: 'new',
        args: ['--no-sandbox']
    });
    
    const page = await browser.newPage();
    
    page.on('console', msg => console.log('PAGE CONSOLE:', msg.type(), msg.text()));
    page.on('pageerror', err => console.log('PAGE ERROR:', err.toString(), err.stack));
    
    console.log('Navigating to http://127.0.0.1:9090/ ...');
    await page.goto('http://127.0.0.1:9090/', { waitUntil: 'networkidle0' });
    
    console.log('Finished loading.');
    await browser.close();
}

test().catch(e => console.error('TEST ERROR:', e));
