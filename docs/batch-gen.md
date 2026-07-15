I want to create a new feature to generate a batch of mockups together. We will reuse a lot of existing code and creating a new tab with a better UI to handle this. Lets call this tab as Batch Generate.

In this UI, I should be able to select a particular category or All categories. Then select number of generations to do an integer between 1 to 100. 

Then I should be able to batch generate mockups for the selected number of product IDs in the particular category. 

Skip any product id without any product images.

Using all the images in the product - In the beginning of the generation prompt Append - "Make the professional mockup of the {color} product". A different mockup will be generated for each color in the dropdown using the product images provided. So each product id can have multiple generations.

In the UI, as we are generating, show a card with Product ID, images selected to generate (zoomable), color selected and the mockup generated. And then a button with Accept (to push png and webp to supabase & remove the card, make the database edits), edit (accept a text input and regenerate with selected images and the improved prompt), Reject (remove the card and discard the generation). During the edit stage, the generated mockups live in Google Drive. When accepted, a copy is saved on supabase storage and the google drive copy is removed.

Make the cards pageneated. The cards are retained and recoverable across sessions.
