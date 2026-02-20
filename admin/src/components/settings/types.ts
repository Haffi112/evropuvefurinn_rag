export interface AppSetting {
  key: string;
  value: string;
  default: string;
  is_overridden: boolean;
  label: string;
  description: string;
  category: "model" | "prompt";
  input_type: "text" | "number" | "textarea";
}
