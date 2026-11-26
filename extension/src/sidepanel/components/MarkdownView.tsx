import React from "react";
import { MarkdownView as SharedMarkdownView, type MarkdownViewProps } from "@ui";

export const MarkdownView: React.FC<MarkdownViewProps> = ({
  compact = true,
  tableClassName = "md-table",
  ...rest
}) => <SharedMarkdownView compact={compact} tableClassName={tableClassName} {...rest} />;
