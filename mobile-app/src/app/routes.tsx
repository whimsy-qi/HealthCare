import { createBrowserRouter } from 'react-router';
import { MobileAppPage } from './pages/MobileAppPage';

export const router = createBrowserRouter([
  { path: '/', Component: MobileAppPage },
  { path: '/app', Component: MobileAppPage },
  { path: '/login', Component: MobileAppPage },
  { path: '/register', Component: MobileAppPage },
  { path: '/profile-setup', Component: MobileAppPage },
  { path: '*', Component: MobileAppPage },
]);
