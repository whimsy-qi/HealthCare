import React, { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import ErrorBoundary from './components/ErrorBoundary';
import { Spin } from 'antd';

const Login = lazy(() => import('./pages/Login'));
const Chat = lazy(() => import('./pages/Chat'));
const Onboarding = lazy(() => import('./pages/Onboarding'));
const Profile = lazy(() => import('./pages/Profile'));
const KnowledgeZone = lazy(() => import('./pages/KnowledgeZone'));
const GraphExplore = lazy(() => import('./pages/GraphExplore'));

const PageLoader = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#F8FAFC' }}>
    <Spin size="large" />
  </div>
);

const App = () => {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<Navigate to="/login" replace />} />
            <Route path="/login" element={<Login />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/knowledge" element={<KnowledgeZone />} />
            <Route path="/graph" element={<GraphExplore />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  );
};

export default App;
