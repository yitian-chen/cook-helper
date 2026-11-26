const paths = {
 logo:'M12 21v-7m0 0C6 14 4 11 4 6c4.3 0 7 2 8 6 0-4.7 2.8-7 8-7 0 5-2.8 8-8 9Z',
 sidebar:'M3 5h18v14H3zM9 5v14',
 photo:'M3 5h18v14H3zM3 16l5-5 4 4 2-2 7 6M16 9h.01',
 kitchen:'M4 12h16l-2 7H6l-2-7ZM3 12h18m-16-3 1-2h12l1 2m-7-2V5',
 settings:'M4 7h16M4 12h16M4 17h16M8 5v4m8 1v4m-6 1v4',
 check:'m5 12 4 4L19 6',
 arrow:'M5 12h14m-6-6 6 6-6 6',
 plus:'M12 5v14M5 12h14',
};
export default function WorkspaceIcon({name}: {name:keyof typeof paths}) {
 return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
