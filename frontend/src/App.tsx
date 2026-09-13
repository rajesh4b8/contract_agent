import { ThemeProvider } from './components/shared/theme-provider';
import { Navigation } from './components/layout/Navigation';
import { ChatPage } from './pages/ChatPage';
import { MattersPage } from './pages/MattersPage';
import { MatterPage } from './pages/MatterPage';
import { DocumentationPage } from './pages/DocumentationPage';
import { SearchPage } from './pages/SearchPage';
import { ErrorBoundary } from './components/shared/ErrorBoundary';
import { ContractHistoryProvider } from './contexts/ContractHistoryContext';
import { useRouter } from './lib/useRouter';
import './App.css';

function App() {
  const { route, navigate } = useRouter();

  const renderPage = () => {
    switch (route.page) {
      case 'matter':
        return (
          <MatterPage
            key={route.matterRef}
            matterRef={route.matterRef!}
            onBack={() => navigate('matters')}
            onOpenMatter={(ref) => navigate('matter', ref)}
          />
        );
      case 'chat':
        return <ChatPage />;
      case 'agents':
        return <DocumentationPage />;
      case 'search':
        return (
          <ErrorBoundary>
            <SearchPage />
          </ErrorBoundary>
        );
      case 'matters':
      default:
        return <MattersPage onOpenMatter={(ref) => navigate('matter', ref)} />;
    }
  };

  return (
    <ContractHistoryProvider>
      <ThemeProvider defaultTheme="light" storageKey="vite-ui-theme">
        <div className="min-h-screen bg-slate-50">
          <div className="mx-auto max-w-7xl p-6">
            <Navigation
              // A matter is still "matters" as far as the nav is concerned:
              // the tab stays lit while you are inside one.
              currentPage={route.page === 'matter' ? 'matters' : route.page}
              onNavigate={(page) => navigate(page)}
            />
            {renderPage()}
          </div>
        </div>
      </ThemeProvider>
    </ContractHistoryProvider>
  );
}

export default App;
