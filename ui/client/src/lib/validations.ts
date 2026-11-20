import { z } from "zod";

export const createTicketSchema = z.object({
  issueTitle: z.string().min(1, "Issue title is required"),
  summary: z.string().min(1, "Summary is required"),
  module: z.string().min(1, "Application is required"),
  createdBy: z.string().min(1, "Created by is required"),
  assigneeTeam: z.string().min(1, "Assignee team is required"),
  priority: z.enum(["HIGH", "MEDIUM", "LOW"]),
  complexity: z.enum(["high", "medium", "low"]),
  description: z.string().min(1, "Description is required"),
});

export type CreateTicketFormValues = z.infer<typeof createTicketSchema>;
