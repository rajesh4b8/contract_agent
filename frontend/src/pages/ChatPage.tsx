import React from 'react';
import { Chat } from '../components/features/contracts';
import { DebugEventPanel } from '../components/features/debug/DebugEventPanel';
import { useDebugEnabled } from '../services/debugApi';

export const ChatPage: React.FC = () => {
  const debugEnabled = useDebugEnabled();

  // Without debug on this page is unchanged: one full-height chat panel.
  if (!debugEnabled) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 h-[calc(100vh-200px)]">
        <Chat />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 h-[calc(100vh-360px)] min-h-[20rem]">
        <Chat />
      </div>
      <DebugEventPanel />
    </div>
  );
};
