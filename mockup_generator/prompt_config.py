MALE_KURTA_PROMPT = """
Role and Objective
Transform the provided garment image into a high fidelity editorial quality mockup of an Indian male kurta pajama suitable for luxury branding on Instagram and WhatsApp.

Execution Checklist
1. Precisely replicate fabric texture, embroidery, border motifs, and color tones from the source images.
2. Use the provided close-ups as the source of truth for intricate details.
3. Place the garment on a well-groomed, graceful male model in a natural, confident full-body pose with head to toe visible.
4. Use a clean white background unless otherwise specified.
5. Frame at 9:16 with recommended resolution 1080x1920.

Garment Specific Instructions
1. Buttons positioned just below the chest.
2. Pajama is white unless otherwise specified.
3. Preserve traditional fit and drape with no modernized alterations.
4. Do not add elements that are not present in the references.

Style Requirements
1. Result must look rich, realistic, polished, and suitable for a luxury magazine campaign.
2. Avoid any artificial look and avoid generic filters or stylization.
3. No hallucination — only details present in the references.

Output and Validation
1. Deliver a single PNG or JPG at 1080x1920.
2. After generation, verify texture, embroidery, color, and proportions match the references. If any mismatch is detected, halt and request clarification.
"""
