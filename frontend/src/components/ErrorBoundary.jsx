import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', height: '100vh', background: '#F8FAFC', color: '#1E293B'
        }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
          <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>页面出现错误</div>
          <div style={{ fontSize: 14, color: '#64748B', marginBottom: 24 }}>
            {this.state.error?.message || '未知错误'}
          </div>
          <button
            onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload(); }}
            style={{
              padding: '8px 24px', background: '#14B8A6', color: '#fff',
              border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 14
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
