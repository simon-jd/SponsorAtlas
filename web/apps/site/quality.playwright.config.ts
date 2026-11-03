import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'tests/quality',workers:1,timeout:30000,reporter:'list',use:{baseURL:'http://127.0.0.1:3100',viewport:{width:1280,height:900},reducedMotion:'reduce',launchOptions:{executablePath:'/usr/bin/google-chrome',args:['--no-sandbox','--disable-gpu']}}});
