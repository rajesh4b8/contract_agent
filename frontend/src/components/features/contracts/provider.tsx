import React, { createContext, useContext, useState, useCallback } from "react";

export type MessagePartType = "user_message" | "ai_message" | "tool_call" | "tool_message" | "history" | "error" | "end";

export type MessagePart = {
    type: MessagePartType;
    content: string;
};

export type Message = {
    id: string;
    type: "user" | "ai";
    parts: Array<MessagePart>;
    generating: boolean;
};

type ChatProviderProps = {
    children: React.ReactNode;
};

type ChatProviderState = {
    messages: Message[];
    addMessage: (message: Message) => void;
    addMessagePart: (id: string, part: MessagePart) => void;
    updateMessageGenerating: (id: string, generating: boolean) => void;
    reset: () => void;
};

const initialState: ChatProviderState = {
    messages: [],
    addMessage: () => null,
    addMessagePart: () => null,
    updateMessageGenerating: () => null,
    reset: () => null
};

const ChatProviderContext = createContext<ChatProviderState>(initialState);

export function ChatProvider({ children }: ChatProviderProps) {
    const [messages, setMessages] = useState<Message[]>([]);

    const addMessage = useCallback((message: Message) => {
        setMessages((prevMessages) => [...prevMessages, message]);
    }, []);

    const addMessagePart = useCallback((messageId: string, part: MessagePart) => {
        setMessages((prevMessages) =>
            prevMessages.map((message) => {
                if (message.id === messageId) {
                    return { ...message, parts: [...message.parts, part] };
                }
                return message;
            })
        );
    }, []);


    const updateMessageGenerating = useCallback((id: string, generating: boolean) => {
        setMessages((prevMessages) =>
            prevMessages.map((message) =>
                message.id === id ? { ...message, generating } : message
            )
        );
    }, []);

    const reset = () => {
        setMessages([]);
    };

    const value = {
        messages,
        addMessage,
        addMessagePart,
        updateMessageGenerating,
        reset
    };

    return (
        <ChatProviderContext.Provider value={value}>
            {children}
        </ChatProviderContext.Provider>
    );
}

export const useChat = () => {
    const context = useContext(ChatProviderContext);

    if (context === undefined) {
        throw new Error("useChat must be used within a ChatProvider");
    }

    return context;
};
