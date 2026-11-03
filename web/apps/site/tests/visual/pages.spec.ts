import {test,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
const id='emp_'+'1'.repeat(64), second='emp_'+'2'.repeat(64);
for(const locale of ['en','zh'])for(const theme of ['light','dark'])for(const [name,path] of Object.entries({search:`/${locale}`,employer:`/${locale}/employers/${id}`,compare:`/${locale}/compare?ids=${id},${second}`,map:`/${locale}/map`})){
 test(`${locale} ${theme} ${name}`,async({page,context})=>{
  await context.addCookies([{name:'sa-theme',value:theme,url:'http://127.0.0.1:3100'}]);
  await page.goto(path);await expect(page.locator('main[role=status]')).toHaveCount(0);await page.locator('h1').waitFor();await page.evaluate(()=>document.fonts.ready);
  if(name==='map'){await expect(page.getByTestId('viz-coverage')).toBeVisible();await page.getByTestId('enable-map').click();await expect(page.getByTestId('viz-fallback')).toBeVisible();}
  await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
  expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
  if(process.env.QUALITY_VISUAL_MUTATION==='1')await page.addStyleTag({content:'h1 {background: #ff00ff !important; padding: 60px !important;}'});
  await expect(page).toHaveScreenshot(`${locale}-${theme}-${name}.png`,{fullPage:true});
 });
}
