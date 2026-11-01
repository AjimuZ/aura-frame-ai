// Photo domain models shared across the AuraFrame AI renderer.

export type PhotoCategory =
  | 'HEADSHOT'
  | 'FULL_BODY'
  | 'ACTIVITY'
  | 'SOCIAL'
  | 'CANDID'
  | 'ELEGANT'
  | 'BAD'
  | 'REJECT';

export type ProfileSlot =
  | 'main_photo'
  | 'second_photo'
  | 'full_body'
  | 'activity_photo'
  | 'social_photo'
  | 'elegant_photo'
  | 'backup'
  | 'reject';

export type PhotoStatus = 'pending' | 'accepted' | 'rejected' | 'selected';

// Local Minimal Gate classification. Only hard_reject/failed_read are excluded from the
// optional OpenAI review; everything else is kept (possibly soft-flagged).
export type GateStatus =
  | 'openai_candidate'
  | 'low_priority_openai_candidate'
  | 'needs_manual_review'
  | 'duplicate_candidate'
  | 'hard_reject'
  | 'failed_read';

export interface GemmaPhotoReview {
  overallScore: number;
  technicalQuality: number;
  faceClarity: number;
  expressionScore: number;
  naturalnessScore: number;
  styleScore: number;
  contextScore: number;
  category: PhotoCategory;
  bestSlot: ProfileSlot;
  strengths: string[];
  weaknesses: string[];
  reason: string;
}

export interface PhotoItem {
  profileId: string;
  id: string;
  filePath: string;
  thumbnailPath: string;
  fileName: string;

  width: number;
  height: number;
  createdAt?: string;
  modifiedAt?: string;

  technicalScore: number;
  blurScore: number;
  brightnessScore: number;
  contrastScore: number;
  likelyScreenshot: boolean;

  faceDetected: boolean;
  faceClarityScore: number;
  faceSizeScore: number;
  faceCenterScore: number;
  eyesVisibleScore: number;
  headYaw: number;
  headPitch: number;
  occlusionScore: number;
  groupPhoto: boolean;
  faceQualityScore: number;
  numberOfFaces: number;

  aestheticScore: number;
  clusterId: number;

  gemmaScore?: GemmaPhotoReview;
  finalScore: number;

  category: PhotoCategory;
  bestSlot: ProfileSlot;

  status: PhotoStatus;
  rejectReason?: string;
  reasonTags: string[];

  // Local Minimal Gate + OpenAI Quality Mode (optional cloud flow). All optional.
  softFlags?: string[];
  gateStatus?: GateStatus | '';
  hardRejectReason?: string;
  eligibleForOpenai?: boolean;
  openaiPriority?: number;
  isNearDuplicate?: boolean;
  openaiBroadScore?: number;
  openaiQualityScore?: number;
  openaiPremiumScore?: number;
  finalJudgeScore?: number;
  consensusScore?: number;
  openaiReason?: string;
  openaiRisk?: string;
  needsManualReview?: boolean;
  failedOpenaiReview?: boolean;
  inTop6?: boolean;
  inTop20?: boolean;
  inTop50?: boolean;
  inTop100?: boolean;
  finalSelectedSlot?: ProfileSlot | '';
  estimatedCostUsd?: number;
}

// Human-readable labels for profile slots used in the Profile Builder.
export const PROFILE_SLOT_LABELS: Record<ProfileSlot, string> = {
  main_photo: 'Main Photo',
  second_photo: 'Second Photo',
  full_body: 'Full Body',
  activity_photo: 'Activity',
  social_photo: 'Social',
  elegant_photo: 'Elegant',
  backup: 'Backup',
  reject: 'Reject',
};

// The six ordered slots that make up the final curated set.
export const FINAL_SET_SLOTS: ProfileSlot[] = [
  'main_photo',
  'full_body',
  'activity_photo',
  'second_photo',
  'social_photo',
  'elegant_photo',
];
