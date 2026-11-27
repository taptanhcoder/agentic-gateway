import {useEffect,useState} from 'react';
export type ThemeMode='light'|'dark'|'system';
export function useTheme(){
 const [theme,setTheme]=useState<ThemeMode>(()=>{try{const saved=localStorage.getItem('gateway-theme');return saved==='light'||saved==='dark'||saved==='system'?saved:'system'}catch{return 'system'}});
 const [systemDark,setSystemDark]=useState(()=>typeof window!=='undefined'&&window.matchMedia('(prefers-color-scheme: dark)').matches);
 useEffect(()=>{const media=window.matchMedia('(prefers-color-scheme: dark)');const sync=()=>setSystemDark(media.matches);sync();media.addEventListener('change',sync);return()=>media.removeEventListener('change',sync)},[]);
 useEffect(()=>{try{localStorage.setItem('gateway-theme',theme)}catch{}},[theme]);
 return {theme,setTheme,dark:theme==='system'?systemDark:theme==='dark'};
}
