import React, { useEffect, useState } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../shared/ui/select';
import { useModels } from '../../../services/modelsApi';

interface ModelPickerProps {
  value: string;
  onChange: (model: string) => void;
  label?: string;
}

/**
 * The model dropdown, in one place.
 *
 * It now actually selects a model: the value reaches the upload as a FormData
 * field the endpoint reads, where before the endpoint declared it as a query
 * parameter and every upload silently ran on the default.
 */
export const ModelPicker: React.FC<ModelPickerProps> = ({ value, onChange, label = 'AI Model' }) => {
  const { models, defaultModel } = useModels();
  const [touched, setTouched] = useState(false);

  // Adopt the backend's default once the catalogue loads, unless the user picked.
  useEffect(() => {
    if (!touched && defaultModel && !value) onChange(defaultModel);
  }, [defaultModel, touched, value, onChange]);

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm font-semibold text-slate-700 whitespace-nowrap">{label}:</label>
      <Select
        value={value || defaultModel}
        onValueChange={(v) => {
          setTouched(true);
          onChange(v);
        }}
      >
        <SelectTrigger className="w-64 border-slate-300">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {models.map((m) => (
            <SelectItem key={m.id} value={m.id} disabled={!m.available}>
              {m.label}
              {!m.available ? ' (API key not set)' : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};
