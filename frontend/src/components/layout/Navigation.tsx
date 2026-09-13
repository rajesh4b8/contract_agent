import React from 'react';
import { Button } from '../shared/ui/button';
import type { PageType } from '../../lib/useRouter';

type NavPage = Exclude<PageType, 'matter'>;

interface NavigationProps {
  currentPage: NavPage;
  onNavigate: (page: NavPage) => void;
}

export const Navigation: React.FC<NavigationProps> = ({ currentPage, onNavigate }) => {
  return (
    <nav className="mb-8 border-b border-slate-200 bg-white rounded-lg shadow-sm">
      <div className="px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-8">
            <h1 className="text-2xl font-bold text-slate-800">Contract Intelligence</h1>
            <div className="flex space-x-1">
              <Button
                variant={currentPage === 'matters' ? 'default' : 'ghost'}
                onClick={() => onNavigate('matters')}
                className={`px-4 py-2 ${currentPage === 'matters' ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200'}`}
              >
                Matters
              </Button>
              <Button
                variant={currentPage === 'chat' ? 'default' : 'ghost'}
                onClick={() => onNavigate('chat')}
                className={`px-4 py-2 ${currentPage === 'chat' ? 'bg-green-600 hover:bg-green-700 text-white' : 'bg-green-50 hover:bg-green-100 text-green-700 border border-green-200'}`}
              >
                Contract Chat
              </Button>
              <Button
                variant={currentPage === 'search' ? 'default' : 'ghost'}
                onClick={() => onNavigate('search')}
                className={`px-4 py-2 ${currentPage === 'search' ? 'bg-teal-600 hover:bg-teal-700 text-white' : 'bg-teal-50 hover:bg-teal-100 text-teal-700 border border-teal-200'}`}
              >
                Enhanced Search
              </Button>
              <Button
                variant={currentPage === 'agents' ? 'default' : 'ghost'}
                onClick={() => onNavigate('agents')}
                className={`px-4 py-2 ${currentPage === 'agents' ? 'bg-purple-600 hover:bg-purple-700 text-white' : 'bg-purple-50 hover:bg-purple-100 text-purple-700 border border-purple-200'}`}
              >
                Documentation
              </Button>
            </div>
          </div>
          <div className="text-sm text-slate-500">
            AI-Powered Legal Document Analysis
          </div>
        </div>
      </div>
    </nav>
  );
};