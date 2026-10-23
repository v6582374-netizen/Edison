import config from './playwright.config';
export default {...config, testMatch: 'youtube-access-maintenance.spec.ts', webServer: undefined};
