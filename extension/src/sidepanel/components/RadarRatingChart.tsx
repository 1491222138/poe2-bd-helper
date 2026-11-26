import React from "react";
import { RadarRatingChart as SharedRadarRatingChart, type RadarRatingChartProps } from "@ui";

export const RadarRatingChart: React.FC<Pick<RadarRatingChartProps, "readiness">> = ({ readiness }) => (
  <SharedRadarRatingChart readiness={readiness} variant="minimal" />
);
