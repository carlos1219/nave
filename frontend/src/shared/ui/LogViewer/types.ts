export interface LogLine {
  seq: number;
  at: string;
  level: "INFO" | "WARNING" | "ERROR";
  message: string;
}
