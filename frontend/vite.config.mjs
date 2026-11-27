import {defineConfig} from 'vite';
export default defineConfig({server:{proxy:{'/portal-api':{target:'http://127.0.0.1:3000',changeOrigin:false}}},build:{sourcemap:false}});
