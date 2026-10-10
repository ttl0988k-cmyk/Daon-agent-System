const puppeteer = require('puppeteer-core');
// Let's check if puppeteer is installed or use chrome
const { execSync } = require('child_process');
const http = require('http');

console.log("Checking page via fetch / node...");
