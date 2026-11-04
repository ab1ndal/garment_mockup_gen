import { useState } from "react";
import {
  type ProductDescription, type ReviewedCopyFields, type TagPick, type TitleEdit, titleFields,
} from "../api";

/** What a review screen holds for Approve to send: description, tags and title. */
export function useReviewCopy(productid: string | null) {
  // null = no draft open; "" is a real (empty) draft and is saved on approve.
  const [draft, setDraft] = useState<string | null>(null);
  const [edited, setEdited] = useState(false);
  const [loaded, setLoaded] = useState<ProductDescription | null>(null);
  // null = tags untouched; sent only together with a description draft.
  const [tags, setTags] = useState<TagPick[] | null>(null);
  const [title, setTitle] = useState<TitleEdit | null>(null);

  const fields = (): ReviewedCopyFields => ({
    ...(productid ? titleFields(title, productid) : {}),
    ...(draft !== null && loaded !== null ? {
      description: draft.trim(),
      expected_description: loaded.description,
      expected_reviewed_at: loaded.description_reviewed_at,
      description_edited: edited,
      ...(tags !== null ? { tags } : {}),
    } : {}),
  });

  return {
    fields,
    draftText: draft?.trim() ?? "",
    section: {
      draft, onDraftChange: setDraft,
      descriptionEdited: edited, onDescriptionEdited: setEdited,
      onLoaded: setLoaded,
      tags, onTagsChange: setTags,
      title, onTitleChange: setTitle,
    },
  };
}
