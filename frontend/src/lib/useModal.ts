import { useState, useCallback } from 'react';

export type ModalType = 'clauses' | 'violations' | 'risk' | 'redlines' | null;

export const useModal = () => {
  const [activeModal, setActiveModal] = useState<ModalType>(null);

  const openModal = useCallback((type: ModalType) => {
    setActiveModal(type);
  }, []);

  const closeModal = useCallback(() => {
    setActiveModal(null);
  }, []);

  const isOpen = useCallback((type: ModalType) => {
    return activeModal === type;
  }, [activeModal]);

  return {
    activeModal,
    openModal,
    closeModal,
    isOpen
  };
};